"""The faces' live link to the daemon (no GTK): snapshots from the socket, events, reconnecting, calls, and the
rule that it never sends usage.refresh by itself."""
from __future__ import annotations

import os
import threading
import time
import unittest

from pixelswitch import protocol as p
from pixelswitch.client import ControlClient
from pixelswitch.gui.link import DaemonLink
from pixelswitch.protocol import ControlError

from .lc_support import DaemonTestCase, FakeDaemon, account_payload, status_payload


def wait_for(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class LinkTests(DaemonTestCase):
    def link(self, **kwargs):
        snapshots = []
        link = DaemonLink(lambda fn: fn(), snapshots.append, env=self.env(), **kwargs)
        self.addCleanup(link.stop)
        return link, snapshots

    def test_the_first_snapshot_has_status_accounts_and_settings(self):
        self.serve({"accounts.list": {"accounts": [account_payload("1", "a@x.com", isActive=True)]},
                    "settings.get": {"settings": {"refreshInterval": 60}}})
        link, snaps = self.link(want_settings=True)
        link.start()
        self.assertTrue(wait_for(lambda: snaps))
        self.assertTrue(snaps[0].connected)
        self.assertEqual([a["email"] for a in snaps[0].accounts], ["a@x.com"])
        self.assertEqual(snaps[0].settings, {"refreshInterval": 60})
        self.assertEqual(snaps[0].app_version, "2.0")

    def test_settings_are_read_only_when_asked(self):
        daemon = self.serve({"accounts.list": {"accounts": []}})
        link, snaps = self.link()
        link.start()
        self.assertTrue(wait_for(lambda: snaps))
        self.assertNotIn("settings.get", daemon.methods_asked())

    def test_it_never_sends_usage_refresh_by_itself(self):
        daemon = self.serve({"accounts.list": {"accounts": []}}, events=[p.event_line("usageUpdated", 1, {})])
        link, snaps = self.link(want_settings=True)
        link.start()
        self.assertTrue(wait_for(lambda: len(snaps) >= 2))
        time.sleep(0.4)
        link.request_snapshot()
        time.sleep(0.6)
        self.assertNotIn("usage.refresh", daemon.methods_asked())
        self.assertIn("events.subscribe", daemon.methods_asked())

    def test_an_event_makes_it_read_again_once_for_a_burst(self):
        reads = []
        events = [p.event_line("usageUpdated", i, {}) for i in range(5)]
        self.serve({"accounts.list": lambda params, request: reads.append(1) or {"accounts": []}}, events=events)
        link, snaps = self.link()
        link.start()
        self.assertTrue(wait_for(lambda: len(snaps) >= 2))
        time.sleep(0.6)
        self.assertEqual(len(snaps), 2)  # the first read, then one for the five events

    def test_events_are_passed_on(self):
        seen = []
        self.serve({"accounts.list": {"accounts": []}}, events=[p.event_line("autoSwitched", 5, {"to": "B"})])
        link = DaemonLink(lambda fn: fn(), lambda s: None, env=self.env(), on_event=seen.append)
        self.addCleanup(link.stop)
        link.start()
        self.assertTrue(wait_for(lambda: seen))
        self.assertEqual((seen[0]["type"], seen[0]["data"]), ("autoSwitched", {"to": "B"}))

    def test_nothing_listening_is_a_disconnected_snapshot_and_it_connects_when_the_daemon_appears(self):
        link, snaps = self.link()
        link.start()
        self.assertTrue(wait_for(lambda: snaps))
        self.assertFalse(snaps[0].connected)
        self.assertIn("not reachable", snaps[0].error)
        self.serve({"accounts.list": {"accounts": []}})
        self.assertTrue(wait_for(lambda: any(s.connected for s in snaps), timeout=12))

    def test_a_daemon_that_goes_away_is_noticed(self):
        daemon = self.serve({"accounts.list": {"accounts": []}})
        link, snaps = self.link()
        link.start()
        self.assertTrue(wait_for(lambda: snaps and snaps[-1].connected))
        daemon.close()
        self.daemons.remove(daemon)
        os.unlink(self.path)
        self.assertTrue(wait_for(lambda: not snaps[-1].connected))

    def test_a_daemon_of_another_protocol_is_a_disconnected_snapshot_with_the_reason(self):
        self.serve({"status.get": status_payload(protocolVersion=99)})
        link, snaps = self.link()
        link.start()
        self.assertTrue(wait_for(lambda: snaps))
        self.assertFalse(snaps[0].connected)
        self.assertIn("speaks remote-control protocol 99", snaps[0].error)

    def test_calls_run_on_a_worker_and_report_back(self):
        daemon = self.serve({"accounts.list": {"accounts": []}, "accounts.switch": {"account": {"id": "x"}},
                             "accounts.remove": lambda params, request: (_ for _ in ()).throw(ControlError("notFound", "No account matches."))})
        link, _ = self.link()
        link.start()
        got = []
        link.call("accounts.switch", {"account": "2"}, lambda result, error: got.append((result, error)))
        link.call("accounts.remove", {"account": "zz"}, lambda result, error: got.append((result, error)))
        self.assertTrue(wait_for(lambda: len(got) == 2))
        self.assertEqual(got[0], ({"account": {"id": "x"}}, None))
        self.assertIsNone(got[1][0])
        self.assertEqual((got[1][1].kind, got[1][1].message), ("notFound", "No account matches."))
        self.assertEqual(daemon.last_params("accounts.switch"), {"account": "2"})

    def test_a_call_with_nothing_listening_reports_the_error(self):
        link = DaemonLink(lambda fn: fn(), lambda s: None, env=self.env())
        self.addCleanup(link.stop)
        link._stop.clear()
        threading.Thread(target=link._call_loop, daemon=True).start()
        got = []
        link.call("status.get", None, lambda r, e: got.append(e))
        self.assertTrue(wait_for(lambda: got))
        self.assertEqual(got[0].kind, "unreachable")

    def test_the_service_is_started_at_most_once_and_only_by_systemctl(self):
        launches = []

        def factory():
            return ControlClient(env={"PIXELSWITCH_CONTROL_SOCKET": self.path, "HOME": self.dir},
                                 launcher=lambda: launches.append(1) or 1)

        snaps = []
        link = DaemonLink(lambda fn: fn(), snaps.append, env={"PIXELSWITCH_CONTROL_SOCKET": self.path, "HOME": self.dir},
                          client_factory=factory)
        self.addCleanup(link.stop)
        link.start()
        self.assertTrue(wait_for(lambda: snaps))
        time.sleep(2.5)  # two more attempts at least
        self.assertEqual(launches, [1])


if __name__ == "__main__":
    unittest.main()
