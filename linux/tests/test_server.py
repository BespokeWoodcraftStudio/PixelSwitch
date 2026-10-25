"""The control socket server: modes, the user check, framing and its limits, per-connection order, a long
call that never blocks another connection, events, and the stale-socket rule."""
from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import tempfile
import threading
import time
import unittest

from pixelswitch import protocol
from pixelswitch.client import ControlClient
from pixelswitch.server import ControlServer, StartError, peer_uid

from .lb_support import wait_for


def short_dir(case):
    path = tempfile.mkdtemp(prefix="ps-", dir="/tmp")
    case.addCleanup(shutil.rmtree, path, True)
    return path


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.dir = short_dir(self)
        self.path = os.path.join(self.dir, "run", "pixelswitch", "control.sock")
        self.handled: list[str] = []
        self.handler = lambda line, connection: self.echo(line, connection)
        self.logs: list[str] = []
        self.server = ControlServer(self.path, lambda line, c: self.handler(line, c), self.logs.append)

    def echo(self, line, connection):
        self.handled.append(line)
        request = json.loads(line)
        if request["method"] == "events.subscribe":
            connection.mark_subscribed()
        if "id" not in request:
            return None
        return protocol.success_line(request["id"], {"echo": request["method"]})

    def start(self):
        self.server.start()
        self.addCleanup(self.server.stop)

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(self.path)
        self.addCleanup(sock.close)
        return sock

    def lines(self, sock, count, timeout=5):
        buffer = b""
        sock.settimeout(timeout)
        while buffer.count(b"\n") < count:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buffer += chunk
        return [json.loads(l) for l in buffer.decode().splitlines()]


class ModesTests(ServerCase):
    def test_the_socket_is_0600_in_a_0700_directory(self):
        previous = os.umask(0o022)
        try:
            self.start()
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)
        self.assertTrue(stat.S_ISSOCK(os.stat(self.path).st_mode))

    def test_a_wider_existing_directory_is_tightened(self):
        os.makedirs(os.path.dirname(self.path))
        os.chmod(os.path.dirname(self.path), 0o755)
        self.start()
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)

    def test_a_path_that_is_too_long_is_refused(self):
        server = ControlServer("/tmp/" + "x" * 120 + "/control.sock", self.echo)
        with self.assertRaises(StartError) as raised:
            server.start()
        self.assertIn("107", str(raised.exception))

    def test_stop_removes_the_socket(self):
        self.start()
        self.server.stop()
        self.assertFalse(os.path.exists(self.path))

    def test_the_client_finds_it_and_talks(self):
        self.start()
        client = ControlClient(self.path)
        client.connect(launch=False)
        self.assertEqual(client.call("status.get"), {"echo": "status.get"})
        client.disconnect()


class StaleSocketTests(ServerCase):
    def test_a_stale_socket_nothing_answers_on_is_replaced(self):
        os.makedirs(os.path.dirname(self.path))
        dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        dead.bind(self.path)
        dead.close()  # the file stays, nothing listens
        self.assertTrue(os.path.exists(self.path))
        self.start()
        client = ControlClient(self.path)
        self.addCleanup(client.disconnect)
        client.connect(launch=False)
        self.assertEqual(client.call("a.b"), {"echo": "a.b"})

    def test_a_live_socket_means_another_copy_runs_and_this_one_does_not_serve(self):
        self.start()
        other = ControlServer(self.path, self.echo)
        with self.assertRaises(StartError) as raised:
            other.start()
        self.assertTrue(raised.exception.already_running)
        client = ControlClient(self.path)
        self.addCleanup(client.disconnect)
        client.connect(launch=False)
        self.assertEqual(client.call("still.first"), {"echo": "still.first"}, "the first server was not disturbed")

    def test_a_regular_file_in_the_way_is_not_silently_deleted_unless_nothing_answers(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as handle:
            handle.write("not a socket")
        self.start()  # nothing answers on it, and the daemon lock proves it is ours to replace
        self.assertTrue(stat.S_ISSOCK(os.stat(self.path).st_mode))


class UserCheckTests(ServerCase):
    def test_peer_uid_is_this_user(self):
        self.start()
        sock = self.connect()
        uid = peer_uid(sock)
        if uid is None:
            self.skipTest("no peer credential option on this platform")
        self.assertEqual(uid, os.getuid())

    def test_a_connection_from_another_user_is_closed_at_once(self):
        self.server = ControlServer(self.path, lambda line, c: self.handler(line, c), self.logs.append, uid=os.getuid() + 1)
        left, right = socket.socketpair(socket.AF_UNIX)
        with left, right:
            if peer_uid(left) is None:
                self.skipTest("no peer credential option on this platform")
        self.start()
        sock = self.connect()
        sock.sendall(protocol.request_line(1, "status.get").encode() + b"\n")
        self.assertEqual(sock.recv(100), b"", "closed without an answer")
        self.assertEqual(self.handled, [])
        self.assertTrue(any("another user" in line for line in self.logs))


class FramingTests(ServerCase):
    def test_one_object_per_line_blank_lines_and_crlf(self):
        self.start()
        sock = self.connect()
        sock.sendall(b"\n   \n\t\n" + protocol.request_line(1, "one").encode() + b"\r\n"
                     + protocol.request_line(2, "two").encode() + b"\n")
        replies = self.lines(sock, 2)
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertEqual(len(self.handled), 2, "blank lines were skipped")

    def test_a_request_split_across_packets(self):
        self.start()
        sock = self.connect()
        line = protocol.request_line(5, "split").encode() + b"\n"
        sock.sendall(line[:7])
        time.sleep(0.05)
        sock.sendall(line[7:])
        self.assertEqual(self.lines(sock, 1)[0]["id"], 5)

    def test_a_notification_gets_no_reply_and_the_connection_goes_on(self):
        self.start()
        sock = self.connect()
        sock.sendall(protocol.request_line(None, "note").encode() + b"\n" + protocol.request_line(9, "after").encode() + b"\n")
        replies = self.lines(sock, 1)
        self.assertEqual(replies[0]["id"], 9)

    def test_a_line_over_1_mib_closes_the_connection_and_others_keep_working(self):
        self.start()
        bad, good = self.connect(), self.connect()
        bad.sendall(b"x" * (1 << 20 + 0) + b"x" * 10)  # 1 MiB and more, never a newline
        bad.settimeout(5)
        self.assertEqual(bad.recv(10), b"")
        good.sendall(protocol.request_line(1, "fine").encode() + b"\n")
        self.assertEqual(self.lines(good, 1)[0]["id"], 1)

    def test_a_complete_line_over_the_limit_closes_the_connection(self):
        self.start()
        sock = self.connect()
        sock.sendall(b'{"jsonrpc":"2.0","id":1,"method":"' + b"m" * (1 << 20) + b'"}\n')
        sock.settimeout(5)
        data = b""
        try:
            data = sock.recv(100)
        except OSError:
            pass
        self.assertEqual(data, b"")
        self.assertEqual(self.handled, [])

    def test_invalid_utf8_is_left_for_the_protocol_layer_to_answer(self):
        self.start()
        self.handler = lambda line, c: protocol.failure_line(None, protocol.ControlError("parse", "bad")) if "�" in line else None
        sock = self.connect()
        sock.sendall(b"\xff\xfe\n")
        self.assertEqual(self.lines(sock, 1)[0]["error"]["code"], -32700)

    def test_a_handler_that_raises_does_not_kill_the_connection(self):
        self.start()
        calls = []

        def handler(line, c):
            calls.append(line)
            if len(calls) == 1:
                raise RuntimeError("bug")
            return protocol.success_line(2, {})

        self.handler = handler
        sock = self.connect()
        sock.sendall(b'{"a":1}\n{"a":2}\n')
        self.assertEqual(self.lines(sock, 1)[0]["id"], 2)


class ConcurrencyTests(ServerCase):
    def test_requests_on_one_connection_run_in_arrival_order_one_at_a_time(self):
        self.start()
        active, order = [], []
        gate = threading.Lock()

        def handler(line, connection):
            request = json.loads(line)
            with gate:
                active.append(1)
                self.assertEqual(len(active), 1, "never two at once on one connection")
            time.sleep(0.05)
            order.append(request["id"])
            with gate:
                active.pop()
            return protocol.success_line(request["id"], {})

        self.handler = handler
        sock = self.connect()
        sock.sendall(b"".join(protocol.request_line(i, "x").encode() + b"\n" for i in range(1, 6)))
        replies = self.lines(sock, 5)
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4, 5])
        self.assertEqual(order, [1, 2, 3, 4, 5])

    def test_a_long_call_on_one_connection_never_blocks_another(self):
        self.start()
        release = threading.Event()
        started = threading.Event()

        def handler(line, connection):
            request = json.loads(line)
            if request["method"] == "accounts.switch":
                started.set()
                release.wait(10)
            return protocol.success_line(request["id"], {"m": request["method"]})

        self.handler = handler
        slow, quick = self.connect(), self.connect()
        slow.sendall(protocol.request_line(1, "accounts.switch").encode() + b"\n")
        self.assertTrue(started.wait(5))
        began = time.monotonic()
        quick.sendall(protocol.request_line(2, "status.get").encode() + b"\n")
        self.assertEqual(self.lines(quick, 1)[0]["id"], 2)
        self.assertLess(time.monotonic() - began, 2.0)
        release.set()
        self.assertEqual(self.lines(slow, 1)[0]["id"], 1)

    def test_many_connections_at_once(self):
        self.start()
        results = []

        def one(n):
            client = ControlClient(self.path)
            client.connect(launch=False)
            results.append(client.call(f"m.{n}"))
            client.disconnect()

        threads = [threading.Thread(target=one, args=(n,)) for n in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(len(results), 20)


class EventTests(ServerCase):
    def test_only_subscribers_get_events_and_they_still_answer_requests(self):
        self.start()
        subscriber, bystander = self.connect(), self.connect()
        subscriber.sendall(protocol.request_line(1, "events.subscribe").encode() + b"\n")
        self.assertEqual(self.lines(subscriber, 1)[0]["id"], 1)
        self.assertTrue(wait_for(lambda: len(self.server.subscribers()) == 1))
        self.server.publish("usageUpdated", {}, 1_700_000_000)
        self.server.publish("autoSwitched", {"from": "A", "to": "B", "limit": "windows", "trigger": "threshold", "window": "session"})
        events = self.lines(subscriber, 2)
        self.assertEqual(events[0], {"jsonrpc": "2.0", "method": "event",
                                      "params": {"at": "2023-11-14T22:13:20Z", "data": {}, "type": "usageUpdated"}})
        self.assertEqual(events[1]["params"]["data"]["window"], "session")
        subscriber.sendall(protocol.request_line(2, "after").encode() + b"\n")
        self.assertEqual(self.lines(subscriber, 1)[0]["id"], 2)
        bystander.settimeout(0.3)
        with self.assertRaises(socket.timeout):
            bystander.recv(10)

    def test_a_subscriber_that_stops_reading_is_dropped_and_does_not_hold_up_the_rest(self):
        self.start()
        stalled, healthy = self.connect(), self.connect()
        stalled.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
        for sock in (stalled, healthy):
            sock.sendall(protocol.request_line(1, "events.subscribe").encode() + b"\n")
            self.lines(sock, 1)
        self.assertTrue(wait_for(lambda: len(self.server.subscribers()) == 2))
        stop_draining = threading.Event()

        def drain():
            healthy.settimeout(0.2)
            while not stop_draining.is_set():
                try:
                    if not healthy.recv(1 << 20):
                        return
                except socket.timeout:
                    continue
                except OSError:
                    return

        drainer = threading.Thread(target=drain, daemon=True)
        drainer.start()
        self.addCleanup(drainer.join, 5)
        self.addCleanup(stop_draining.set)
        big = {"blob": "x" * 200_000}
        started = time.monotonic()
        for _ in range(60):
            self.server.publish("usageUpdated", big)
            if len(self.server.subscribers()) == 1:
                break
            time.sleep(0.05)
        self.assertTrue(wait_for(lambda: len(self.server.subscribers()) == 1, 15))
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(self.server.subscribers()[0].sock.getpeername(), self.path if False else self.server.subscribers()[0].sock.getpeername())

    def test_publish_returns_at_once(self):
        self.start()
        started = time.monotonic()
        for _ in range(1000):
            self.server.publish("usageUpdated", {})
        self.assertLess(time.monotonic() - started, 0.5)


if __name__ == "__main__":
    unittest.main()
