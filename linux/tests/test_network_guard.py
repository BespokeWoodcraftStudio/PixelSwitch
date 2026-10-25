"""The guard that fails the whole suite if a test reaches anthropic.com, claude.com or github.com."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import unittest

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
        self.addCleanup(network_guard.clear)
        before = len(network_guard.violations)
        for attempt in (lambda: socket.create_connection(("api.anthropic.com", 443), timeout=1),
                        lambda: socket.getaddrinfo("github.com", 443),
                        lambda: socket.gethostbyname("claude.com"),
                        lambda: self.sock().connect(("console.anthropic.com", 443)),
                        lambda: self.sock().connect_ex(("github.com", 443))):
            with self.assertRaises(OSError):
                attempt()
        self.assertEqual(len(network_guard.violations) - before, 5)
        network_guard.clear()  # these were deliberate: the run must not fail because of them

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
