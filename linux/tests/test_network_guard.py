"""The guard that fails the whole suite if a test reaches anthropic.com, claude.com or github.com."""
from __future__ import annotations

import http.client
import os
import socket
import subprocess
import sys
import unittest
import urllib.request
from unittest import mock

from . import network_guard
from .lb_support import tmpdir

LINUX_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GuardTests(unittest.TestCase):
    def sock(self):
        sock = socket.socket()
        self.addCleanup(sock.close)
        return sock

    def test_it_is_installed_for_every_run_of_the_suite(self):
        self.assertTrue(network_guard._installed)

    def test_blocked_hosts(self):
        for host in ("api.anthropic.com", "console.anthropic.com", "anthropic.com", "claude.com", "www.claude.com",
                     "github.com", "API.GitHub.com.", "release-assets.githubusercontent.com", b"github.com"):
            self.assertTrue(network_guard.blocked(host), host)
        for host in ("127.0.0.1", "localhost", "example.invalid", "notgithub.com", "github.com.example.invalid",
                     "myanthropic.com", "", None):
            self.assertFalse(network_guard.blocked(host), host)

    def test_a_connection_attempt_is_refused_and_recorded_without_any_network_traffic(self):
        """The five deliberate refusals go into this test's OWN recorder: what other tests recorded before it
        is neither read nor erased (review FL1/FL2 re-review: ``clear`` wiped every earlier violation)."""
        before = list(network_guard.violations)
        with network_guard.scoped() as mine:
            for attempt in (lambda: socket.create_connection(("api.anthropic.com", 443), timeout=1),
                            lambda: socket.getaddrinfo("github.com", 443),
                            lambda: socket.gethostbyname("claude.com"),
                            lambda: self.sock().connect(("console.anthropic.com", 443)),
                            lambda: self.sock().connect_ex(("github.com", 443))):
                with self.assertRaises(OSError):
                    attempt()
        self.assertEqual(len(mine), 5)
        self.assertEqual(network_guard.violations, before, "the run-wide list is exactly as it was")

    def test_a_violation_recorded_before_the_guard_test_still_fails_the_run(self):
        code = ("import sys\nsys.path.insert(0, %r)\nfrom tests import network_guard\nimport socket\n"
                "try:\n    socket.getaddrinfo('api.anthropic.com', 443)\nexcept OSError:\n    pass\n"
                "with network_guard.scoped() as mine:\n"
                "    for host in ('github.com', 'claude.com'):\n"
                "        try:\n            socket.getaddrinfo(host, 443)\n        except OSError:\n            pass\n"
                "print('own recorder holds', len(mine))\n" % LINUX_DIR)
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("own recorder holds 2", done.stdout)
        self.assertIn("api.anthropic.com", done.stderr)
        self.assertNotIn("getaddrinfo('github.com')", done.stderr, "the self-test's own refusals are not reported")

    def test_a_violation_from_another_thread_is_not_swallowed_by_a_scope(self):
        code = ("import sys, threading\nsys.path.insert(0, %r)\nfrom tests import network_guard\nimport socket\n"
                "def other():\n    try:\n        socket.getaddrinfo('api.anthropic.com', 443)\n    except OSError:\n        pass\n"
                "with network_guard.scoped():\n    worker = threading.Thread(target=other)\n    worker.start()\n    worker.join()\n"
                % LINUX_DIR)
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 1, done.stderr)

    def test_a_proxy_in_the_environment_is_removed_and_a_proxy_set_later_fails_closed(self):
        environment = dict(os.environ, HTTPS_PROXY="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9",
                           ALL_PROXY="socks5://127.0.0.1:9", http_proxy="http://127.0.0.1:9")
        code = ("import os, sys, urllib.request\nsys.path.insert(0, %r)\nfrom tests import network_guard\n"
                "print(sorted(k for k in os.environ if k.lower().endswith('_proxy')))\n"
                "print(urllib.request.ProxyHandler().proxies)\n"
                "print(sorted(network_guard.stripped_proxy_variables))\n"
                "os.environ['https_proxy'] = 'http://127.0.0.1:9'\n"
                "print(urllib.request.ProxyHandler().proxies)\n" % LINUX_DIR)
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=environment)
        lines = done.stdout.splitlines()
        self.assertEqual(lines[0], "[]", "none left for this process or its children")
        self.assertEqual(lines[1], "{}")
        self.assertEqual(lines[2], "['ALL_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']")
        self.assertEqual(lines[3], "{}", "a proxy configured later is never used")
        self.assertEqual(done.returncode, 1, "and is a violation: " + done.stderr)
        self.assertIn("proxy('https')", done.stderr)

    def test_a_proxy_path_cannot_carry_a_blocked_host(self):
        with network_guard.scoped() as mine, mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:9"}):
            self.assertEqual(urllib.request.getproxies(), {})
            self.assertEqual(urllib.request.ProxyHandler().proxies, {})
            connection = http.client.HTTPSConnection("127.0.0.1", 9)
            with self.assertRaises(OSError):
                connection.set_tunnel("api.anthropic.com", 443)
            connection.set_tunnel("example.invalid", 443)
        self.assertEqual(mine, ["proxy('https')", "proxy('https')", "set_tunnel('api.anthropic.com')"])

    def test_localhost_and_unix_sockets_are_not_touched(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        self.addCleanup(server.close)
        client = socket.create_connection(server.getsockname(), timeout=2)
        client.close()
        self.assertEqual(network_guard.violations, [])

    def test_a_violation_makes_the_process_exit_1_even_when_the_code_swallows_the_error(self):
        code = ("import sys\nsys.path.insert(0, %r)\nfrom tests import network_guard\nimport socket\n"
                "try:\n    socket.create_connection(('api.anthropic.com', 443), timeout=1)\nexcept OSError:\n    pass\n"
                "print('finished normally')\n" % LINUX_DIR)
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("finished normally", done.stdout)
        self.assertIn("api.anthropic.com", done.stderr)

    def test_a_clean_process_exits_0(self):
        code = "import sys\nsys.path.insert(0, %r)\nfrom tests import network_guard\n" % LINUX_DIR
        self.assertEqual(subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=60).returncode, 0)

    def test_a_child_process_carries_the_guard_and_reports_to_a_log(self):
        directory = tmpdir(self)
        environment = dict(os.environ, **network_guard.child_environment(directory))
        code = "import socket\ntry:\n    socket.getaddrinfo('api.anthropic.com', 443)\nexcept OSError:\n    pass\n"
        subprocess.run([sys.executable, "-c", code], env=environment, capture_output=True, timeout=60)
        self.assertEqual(network_guard.read_child_log(os.path.join(directory, "netguard.log")),
                         ["getaddrinfo('api.anthropic.com')"])


if __name__ == "__main__":
    unittest.main()
