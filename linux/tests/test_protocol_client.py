"""The Linux control client against a scripted server on a real Unix socket: connect, call with a
timeout, events, the 1 MiB line limit, launching the service once, socket resolution (spec 5.2)
and the CLI exit-code mapping (control.md 3.1)."""
from __future__ import annotations

import os
import shutil
import socket
import tempfile
import threading
import time
import unittest

from pixelswitch import client as cl
from pixelswitch import protocol as p
from pixelswitch.client import ClientError, ControlClient
from pixelswitch.protocol import ControlError


class FakeServer:
    """A one-purpose Unix socket server. ``handler(request, send)`` is called for every request;
    ``send(text)`` writes a raw line."""

    def __init__(self, path, handler=None, on_connect=None):
        self.path = path
        self.handler = handler or self.default
        self.on_connect = on_connect
        self.requests = []
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(path)
        self.listener.listen(8)
        self.stop = threading.Event()
        self.connections = []
        self.thread = threading.Thread(target=self.accept_loop, daemon=True)
        self.thread.start()

    def default(self, request, send):
        send(p.success_line(request.id, {"echo": request.method}))

    def accept_loop(self):
        self.listener.settimeout(0.1)
        while not self.stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except (socket.timeout, OSError):
                continue
            self.connections.append(conn)
            threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

    def serve(self, conn):
        try:
            self.serve_connection(conn)
        except OSError:
            pass  # the test closed the connection under us

    def serve_connection(self, conn):
        def send(text):
            conn.sendall(text.encode("utf-8") + b"\n")

        if self.on_connect:
            self.on_connect(send, conn)
        buffer = b""
        conn.settimeout(0.1)
        while not self.stop.is_set():
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                request = p.decode_request(line)
                self.requests.append(request)
                self.handler(request, send)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)   # no new connection after this
        for conn in self.connections:
            try:
                conn.close()
            except OSError:
                pass
        self.listener.close()


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="psc")
        self.path = os.path.join(self.dir, "c.sock")
        self.servers = []
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            c.disconnect()
        for s in self.servers:
            s.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def serve(self, handler=None, on_connect=None, path=None):
        server = FakeServer(path or self.path, handler, on_connect)
        self.servers.append(server)
        return server

    def client(self, **kwargs):
        kwargs.setdefault("env", {"PIXELSWITCH_NO_LAUNCH": "1", "HOME": self.dir})
        c = ControlClient(self.path, **kwargs)
        self.clients.append(c)
        return c


class CallTests(ClientTestCase):
    def test_a_call_goes_through_the_real_socket_and_back(self):
        self.serve()
        c = self.client()
        c.connect(launch=False)
        self.assertEqual(c.call("status.get"), {"echo": "status.get"})
        self.assertEqual(c.path, self.path)

    def test_params_and_ids_are_sent(self):
        server = self.serve()
        c = self.client()
        c.connect(launch=False)
        c.call("accounts.switch", {"account": "2"})
        c.call("status.get")
        first, second = server.requests
        self.assertEqual((first.id, first.method, first.params), (1, "accounts.switch", {"account": "2"}))
        self.assertEqual((second.id, second.params), (2, None))

    def test_an_error_reply_reaches_the_client_as_a_control_error(self):
        self.serve(lambda r, send: send(p.failure_line(
            r.id, ControlError("notFound", 'No account matches "nobody".', ["a@x.com", "b@x.com", "c@x.com"]))))
        c = self.client()
        c.connect(launch=False)
        with self.assertRaises(ControlError) as caught:
            c.call("accounts.switch", {"account": "nobody"})
        self.assertEqual(caught.exception.kind, "notFound")
        self.assertEqual(len(caught.exception.candidates), 3)

    def test_events_and_other_replies_are_skipped_while_waiting(self):
        def handler(request, send):
            send(p.event_line("usageUpdated", 1_790_000_000))
            send(p.success_line(request.id + 100, {"stray": True}))
            send("not json at all")
            send("[1,2]")
            send(p.success_line(request.id, {"ok": True}))

        self.serve(handler)
        c = self.client()
        c.connect(launch=False)
        self.assertEqual(c.call("status.get"), {"ok": True})

    def test_a_null_result_is_none(self):
        self.serve(lambda r, send: send(p.success_line(r.id, None)))
        c = self.client()
        c.connect(launch=False)
        self.assertIsNone(c.call("accounts.signIn.status"))

    def test_a_silent_server_times_out(self):
        self.serve(lambda r, send: None)
        c = self.client()
        c.connect(launch=False)
        started = time.monotonic()
        with self.assertRaises(ClientError) as caught:
            c.call("status.get", timeout=0.3)
        self.assertEqual(caught.exception.kind, "timedOut")
        self.assertEqual(str(caught.exception), "PixelSwitch did not answer status.get in time.")
        self.assertLess(time.monotonic() - started, 3)

    def test_slow_calls_wait_at_least_120_seconds_and_others_do_not(self):
        def slow(request, send):
            time.sleep(0.6)
            send(p.success_line(request.id, {"echo": request.method}))

        self.serve(slow)
        c = self.client()
        c.connect(launch=False)
        # an ordinary call gives up at its own (short) timeout...
        with self.assertRaises(ClientError) as caught:
            c.call("status.get", timeout=0.2)
        self.assertEqual(caught.exception.kind, "timedOut")
        time.sleep(0.8)  # let the late reply arrive; it belongs to a call nobody waits for any more
        # ...a slow one is given the floor of 120 s, so a 0.2 s timeout still waits for the reply
        self.assertEqual(c.call("usage.refresh", timeout=0.2), {"echo": "usage.refresh"})
        self.assertEqual(c.call("accounts.switch", {"account": "1"}, timeout=0.2), {"echo": "accounts.switch"})
        # with the floor off the timeout is exact
        with self.assertRaises(ClientError):
            c.call("usage.refresh", timeout=0.2, slow_floor=False)

    def test_a_server_that_goes_away_closes_the_connection(self):
        def handler(request, send):
            raise SystemExit  # never reached in the thread; we close from outside instead

        server = self.serve(lambda r, send: [c.close() for c in server.connections])
        c = self.client()
        c.connect(launch=False)
        with self.assertRaises(ClientError) as caught:
            c.call("status.get", timeout=3)
        self.assertEqual(caught.exception.kind, "closed")
        self.assertEqual(str(caught.exception), "PixelSwitch closed the connection.")
        self.assertFalse(c.connected)
        with self.assertRaises(ClientError):
            c.call("status.get", timeout=1)

    def test_writing_to_a_vanished_server_raises_instead_of_killing_the_process(self):
        server = self.serve()
        c = self.client()
        c.connect(launch=False)
        server.close()
        time.sleep(0.2)
        with self.assertRaises(ClientError):
            for _ in range(5):
                c.call("status.get", timeout=1)

    def test_a_line_over_one_mebibyte_closes_the_connection(self):
        def handler(request, send):
            send("x" * (p.MAX_LINE_BYTES + 4096))

        self.serve(handler)
        c = self.client()
        c.connect(launch=False)
        with self.assertRaises(ClientError) as caught:
            c.call("status.get", timeout=5)
        self.assertEqual(caught.exception.kind, "closed")

    def test_a_large_but_legal_reply_is_read(self):
        big = {"blob": "y" * (p.MAX_LINE_BYTES // 2)}
        self.serve(lambda r, send: send(p.success_line(r.id, big)))
        c = self.client()
        c.connect(launch=False)
        self.assertEqual(c.call("status.get", timeout=10), big)

    def test_next_event_after_subscribing(self):
        def handler(request, send):
            send(p.success_line(request.id, {"subscribed": True}))
            send(p.event_line("autoSwitched", 1_790_000_000, p.auto_switched_data("a", "b", "windows", "threshold", "weekly", relaxed=False)))

        self.serve(handler)
        c = self.client()
        c.connect(launch=False)
        self.assertEqual(c.call("events.subscribe"), {"subscribed": True})
        event = c.next_event(timeout=3)
        self.assertEqual(event["type"], "autoSwitched")
        self.assertEqual(event["data"]["window"], "weekly")

    def test_next_event_returns_none_at_the_deadline(self):
        self.serve()
        c = self.client()
        c.connect(launch=False)
        self.assertIsNone(c.next_event(timeout=0.2))

    def test_events_pushed_on_connect_are_not_lost_to_a_call(self):
        self.serve(on_connect=lambda send, conn: send(p.event_line("usageUpdated", 1)))
        c = self.client()
        c.connect(launch=False)
        self.assertEqual(c.next_event(timeout=3)["type"], "usageUpdated")

    def test_peer_credentials_of_a_server_running_as_this_user_pass(self):
        self.serve()
        c = self.client()
        c.connect(launch=False)
        self.assertTrue(c.connected)


class ConnectTests(ClientTestCase):
    def test_nothing_listening_is_unreachable(self):
        c = self.client()
        with self.assertRaises(ClientError) as caught:
            c.connect()
        self.assertEqual(caught.exception.kind, "unreachable")
        self.assertEqual(str(caught.exception), f"PixelSwitch is not reachable: nothing is listening at {self.path}")

    def test_no_launch_variable_stops_the_service_start(self):
        launched = []
        c = self.client(launcher=lambda: launched.append(1) or 0)
        with self.assertRaises(ClientError):
            c.connect()
        self.assertEqual(launched, [])

    def test_launch_false_stops_the_service_start(self):
        launched = []
        c = ControlClient(self.path, env={"HOME": self.dir}, launcher=lambda: launched.append(1) or 0)
        with self.assertRaises(ClientError):
            c.connect(launch=False)
        self.assertEqual(launched, [])

    def test_the_service_is_started_once_and_polled_for(self):
        launched = []

        def launcher():
            launched.append(1)
            threading.Timer(0.4, lambda: self.serve()).start()
            return 0

        c = ControlClient(self.path, env={"HOME": self.dir}, launcher=launcher)
        self.clients.append(c)
        c.connect(wait=5)
        self.assertEqual(launched, [1])
        self.assertEqual(c.call("status.get"), {"echo": "status.get"})

    def test_a_service_that_never_opens_its_socket(self):
        launched = []
        c = ControlClient(self.path, env={"HOME": self.dir}, launcher=lambda: launched.append(1) or 0)
        with self.assertRaises(ClientError) as caught:
            c.connect(wait=0.6)
        self.assertEqual(launched, [1])
        self.assertEqual(caught.exception.kind, "unreachable")
        self.assertIn("did not open its socket within 0 seconds", str(caught.exception))

    def test_a_service_that_cannot_be_started(self):
        c = ControlClient(self.path, env={"HOME": self.dir}, launcher=lambda: 5)
        with self.assertRaises(ClientError) as caught:
            c.connect()
        self.assertIn("not installed, or its service could not be started", str(caught.exception))

    def test_a_dead_socket_file_is_unreachable_not_a_crash(self):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.path)
        listener.close()
        self.assertTrue(os.path.exists(self.path))
        with self.assertRaises(ClientError):
            self.client().connect()

    def test_a_path_too_long_for_the_kernel_is_unreachable(self):
        c = ControlClient("/tmp/" + "x" * 200, env={"PIXELSWITCH_NO_LAUNCH": "1"})
        with self.assertRaises(ClientError):
            c.connect()

    def test_the_first_candidate_that_answers_wins(self):
        other = os.path.join(self.dir, "o.sock")
        self.serve(path=other)
        state = os.path.join(self.dir, ".local", "state", "pixelswitch")
        os.makedirs(state)
        with open(os.path.join(state, "socket-path"), "w") as handle:
            handle.write(other + "\n")
        c = ControlClient(None, env={"PIXELSWITCH_NO_LAUNCH": "1", "HOME": self.dir})
        self.clients.append(c)
        c.connect()
        self.assertEqual(c.path, other)

    def test_start_service_uses_systemctl_user_and_never_the_daemon(self):
        from unittest import mock

        with mock.patch.object(cl.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(cl.start_service(), 0)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["systemctl", "--user", "start", "pixelswitch.service"])
        with mock.patch.object(cl.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(cl.start_service(), -1)


class SocketResolutionTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="psh")
        self.state = os.path.join(self.home, ".local", "state", "pixelswitch")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def write_recorded(self, text):
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, "socket-path"), "w") as handle:
            handle.write(text)

    def test_the_override_is_the_only_path(self):
        env = {"PIXELSWITCH_CONTROL_SOCKET": "/x/y.sock", "HOME": self.home, "XDG_RUNTIME_DIR": "/run/user/1"}
        self.assertEqual(cl.socket_candidates(env, 1000), ["/x/y.sock"])

    def test_the_order_of_spec_5_2(self):
        self.write_recorded("/recorded/control.sock\n")
        env = {"HOME": self.home, "XDG_RUNTIME_DIR": "/run/user/1000"}
        self.assertEqual(cl.socket_candidates(env, 1000), [
            "/recorded/control.sock",
            "/run/user/1000/pixelswitch/control.sock",
            os.path.join(self.state, "control.sock"),
        ])

    def test_without_xdg_runtime_dir_the_uid_path_is_used(self):
        self.assertEqual(cl.socket_candidates({"HOME": self.home}, 1234), [
            "/run/user/1234/pixelswitch/control.sock",
            os.path.join(self.state, "control.sock"),
        ])

    def test_xdg_runtime_dir_and_the_uid_path_are_both_tried_when_they_differ(self):
        got = cl.socket_candidates({"HOME": self.home, "XDG_RUNTIME_DIR": "/tmp/xdg"}, 1234)
        self.assertEqual(got[0], "/tmp/xdg/pixelswitch/control.sock")
        self.assertEqual(got[1], "/run/user/1234/pixelswitch/control.sock")

    def test_duplicates_are_dropped(self):
        self.write_recorded("/run/user/1000/pixelswitch/control.sock")
        got = cl.socket_candidates({"HOME": self.home, "XDG_RUNTIME_DIR": "/run/user/1000"}, 1000)
        self.assertEqual(got.count("/run/user/1000/pixelswitch/control.sock"), 1)

    def test_a_recorded_path_that_is_not_absolute_is_ignored(self):
        self.write_recorded("relative/control.sock")
        self.assertNotIn("relative/control.sock", cl.socket_candidates({"HOME": self.home}, 1))
        self.write_recorded("")
        self.assertEqual(len(cl.socket_candidates({"HOME": self.home}, 1)), 2)

    def test_the_path_comes_from_home_only(self):
        got = cl.socket_candidates({"HOME": self.home, "XDG_STATE_HOME": "/elsewhere"}, 1)
        self.assertEqual(got[-1], os.path.join(self.home, ".local", "state", "pixelswitch", "control.sock"))

    def test_launch_allowed(self):
        self.assertTrue(cl.launch_allowed({}))
        self.assertFalse(cl.launch_allowed({"PIXELSWITCH_NO_LAUNCH": "1"}))
        self.assertTrue(cl.launch_allowed({"PIXELSWITCH_NO_LAUNCH": "0"}))


class ExitCodeTests(unittest.TestCase):
    def test_control_errors(self):
        code = lambda kind: cl.exit_code_for(ControlError(kind, "m"))  # noqa: E731
        self.assertEqual([code("busy"), code("notFound"), code("ambiguous"), code("invalidValue"), code("invalidParams")],
                         [3, 4, 4, 2, 2])
        for kind in ("parse", "invalidRequest", "methodNotFound", "claudeUnavailable", "protocolMismatch", "failed"):
            self.assertEqual(code(kind), 1, kind)

    def test_transport_errors(self):
        self.assertEqual(cl.exit_code_for(ClientError("unreachable", "x")), 5)
        self.assertEqual(cl.exit_code_for(ClientError("timedOut", "m")), 1)
        self.assertEqual(cl.exit_code_for(ClientError("closed")), 1)
        self.assertEqual(cl.exit_code_for(RuntimeError("x")), 1)

    def test_the_constants(self):
        self.assertEqual((cl.EXIT_OK, cl.EXIT_FAILED, cl.EXIT_USAGE, cl.EXIT_BUSY, cl.EXIT_NOT_FOUND, cl.EXIT_UNREACHABLE),
                         (0, 1, 2, 3, 4, 5))


class TimeoutAndGateTests(unittest.TestCase):
    def test_the_slow_call_floor_is_120_seconds(self):
        for method in ("accounts.switch", "accounts.addCurrent", "usage.refresh"):
            self.assertEqual(cl.call_timeout(method, 30), 120, method)
            self.assertEqual(cl.call_timeout(method, 300), 300, method)
        self.assertEqual(cl.call_timeout("status.get", 30), 30)
        self.assertEqual(cl.call_timeout("accounts.list", 5), 5)

    def test_protocol_must_equal_two(self):
        cl.check_protocol({"protocolVersion": 2, "appVersion": "2.0"})
        for bad in ({"protocolVersion": 3, "appVersion": "9.9"}, {"protocolVersion": 1}, {}, {"protocolVersion": True},
                    {"protocolVersion": "2"}):
            with self.assertRaises(ControlError) as caught:
                cl.check_protocol(bad)
            self.assertEqual(caught.exception.kind, "protocolMismatch")
        with self.assertRaises(ControlError) as caught:
            cl.check_protocol({"protocolVersion": 3, "appVersion": "9.9"})
        self.assertEqual(caught.exception.message,
                         "PixelSwitch 9.9 speaks remote-control protocol 3, but this pixelswitch speaks 2. "
                         "Quit and reopen PixelSwitch, then run the command again.")

    def test_features_gate_the_two_new_methods(self):
        new = {"appVersion": "2.0", "features": ["priority", "splitThresholds"]}
        cl.require_feature(new, "splitThresholds")
        cl.require_feature(new, "priority")
        old = {"appVersion": "1.9"}
        with self.assertRaises(ControlError) as caught:
            cl.require_feature(old, "splitThresholds")
        self.assertEqual(caught.exception.message,
                         "This PixelSwitch (1.9) cannot set separate thresholds; update it.")
        self.assertEqual(cl.exit_code_for(caught.exception), 1)
        with self.assertRaises(ControlError) as caught:
            cl.require_feature({"appVersion": "1.9", "features": []}, "priority")
        self.assertEqual(caught.exception.message, "This PixelSwitch (1.9) cannot set priorities; update it.")
        self.assertFalse(cl.has_feature({"features": "priority"}, "priority"))

    def test_choices_line(self):
        self.assertEqual(cl.candidates_text(["a", "b"]), "  choices: a, b")


if __name__ == "__main__":
    unittest.main()
